#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple script: Takes a SAM.gov link and downloads all the files.
Usage: python download_sam_files.py

Supports two download methods:
1. API-based: Uses resourceLinks from the SAM.gov API (faster, more reliable)
2. Selenium-based: Falls back to clicking 'Download All' button if no resourceLinks
"""

import os
import json
import requests
import re
from datetime import datetime
from main import fetch_contracts, fetch_ui_link_data
from google_drive_utils import extract_text_from_file_content, get_filename_from_cd
from gemini import filter_vendor_relevant_content, has_site_visit

# Configuration
DOWNLOADS_DIR = 'downloaded_files'
EXTRACTED_TEXT_DIR = 'extracted_text'
ENABLE_CONTENT_FILTERING = True  # Set to False to disable Gemini filtering
USE_API_DOWNLOAD = True  # Set to True to prefer API-based downloads over Selenium


def download_files_from_resource_links(resource_links, notice_id):
    """
    Download files directly from SAM.gov API resourceLinks.
    This is much faster and more reliable than using Selenium.
    
    Parameters:
    - resource_links: List of direct download URLs from the API
    - notice_id: The notice ID for organizing downloaded files
    
    Returns:
    - List of downloaded file paths
    """
    if not resource_links:
        print("No resource links provided")
        return []
    
    print(f"Downloading {len(resource_links)} files via API for notice: {notice_id}")
    
    # Create download directory
    download_dir = os.path.join(DOWNLOADS_DIR, notice_id)
    os.makedirs(download_dir, exist_ok=True)
    
    downloaded_files = []
    
    for i, link in enumerate(resource_links):
        try:
            print(f"  Downloading file {i+1}/{len(resource_links)}: {link[:80]}...")
            
            # Make the request with streaming to handle large files
            response = requests.get(link, timeout=120, stream=True)
            response.raise_for_status()
            
            # Get filename from Content-Disposition header
            cd = response.headers.get('content-disposition')
            filename = get_filename_from_cd(cd)
            
            if not filename:
                # Try to extract filename from URL or use fallback
                url_filename = link.split('/')[-2] if '/download' in link else link.split('/')[-1]
                filename = f"{notice_id}_attachment_{i+1}.dat"
                print(f"    Warning: Could not determine filename, using: {filename}")
            else:
                # Clean filename of invalid characters
                filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
            
            # Save the file
            file_path = os.path.join(download_dir, filename)
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            file_size = os.path.getsize(file_path)
            print(f"    ✅ Downloaded: {filename} ({file_size:,} bytes)")
            downloaded_files.append(file_path)
            
        except requests.exceptions.Timeout:
            print(f"    ❌ Timeout downloading file {i+1}")
        except requests.exceptions.RequestException as e:
            print(f"    ❌ Error downloading file {i+1}: {e}")
        except Exception as e:
            print(f"    ❌ Unexpected error downloading file {i+1}: {e}")
    
    print(f"📦 Downloaded {len(downloaded_files)}/{len(resource_links)} files successfully")
    return downloaded_files


def extract_text_from_downloaded_files(downloaded_files, notice_id):
    """
    Extract text content from downloaded files.
    
    Parameters:
    - downloaded_files: List of file paths to process
    - notice_id: The notice ID for organizing output
    
    Returns:
    - Combined text content from all files
    """
    if not downloaded_files:
        return ""
    
    complete_text = ""
    
    for file_path in downloaded_files:
        try:
            filename = os.path.basename(file_path)
            print(f"\nProcessing file: {filename}")
            
            # Read file content
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            print(f"  File size: {len(file_content):,} bytes")
            
            # Extract text using existing utility
            extracted_text = extract_text_from_file_content(filename, file_content)
            
            # Add to complete text
            complete_text += f"\nFILENAME: {filename}\n"
            complete_text += "-" * 50 + "\n"
            
            if extracted_text:
                print(f"  Extracted {len(extracted_text):,} characters of text")
                complete_text += extracted_text + "\n\n"
            else:
                complete_text += "[No text content - binary file or unsupported format]\n\n"
                print(f"  No text extracted (binary file or unsupported format)")
                
        except Exception as e:
            print(f"  ❌ Error processing {file_path}: {e}")
            complete_text += f"[ERROR processing file: {e}]\n\n"
    
    return complete_text


def parse_sam_ui_metadata(ui_text):
    """
    Use Gemini to extract key metadata from SAM UI text.
    Returns a dict with extracted information and deadline flags.
    """
    from gemini import extract_sam_metadata
    return extract_sam_metadata(ui_text)


def click_download_all_button(sam_url, notice_id):
    """Click the 'Download All' button on SAM.gov page to get zip file."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import time
    
    print(f"Looking for 'Download All' button on: {sam_url}")
    
    # Configure Chrome options for downloading
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Set download directory specific to this notice
    download_dir = os.path.abspath(os.path.join(DOWNLOADS_DIR, notice_id))
    os.makedirs(download_dir, exist_ok=True)
    
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    driver = None
    try:
        # Initialize driver
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.set_page_load_timeout(60)
        # Enable downloads in headless mode
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": download_dir,
        })
        
        # Load page
        print("Loading SAM.gov page...")
        driver.get(sam_url)
        
        # Wait for page to load
        print("Waiting for page to load...")
        time.sleep(10)
        
        # Try different ways to find the Download All button
        download_button = None
        
        # Method 1: Look for button with "Download All" text
        try:
            download_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Download All')]"))
            )
            print("Found 'Download All' button by text")
        except:
            pass
        
        # Method 2: Look for link with "Download All" text
        if not download_button:
            try:
                download_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Download All')]"))
                )
                print("Found 'Download All' link by text")
            except:
                pass
        
        # Method 3: Look for elements with download-related classes or attributes
        if not download_button:
            try:
                download_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//*[contains(@class, 'download') and contains(@class, 'all')]"))
                )
                print("Found download button by class")
            except:
                pass
        
        # Method 4: Look for any button/link containing "download" and "all" (case insensitive)
        if not download_button:
            try:
                download_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download') and contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'all')]"))
                )
                print("Found download button by case-insensitive text search")
            except:
                pass
        
        if download_button:
            print("SUCCESS: Found Download All button!")
            print(f"Button text: '{download_button.text}'")
            print(f"Button tag: {download_button.tag_name}")
            
            # Scroll to the button to make sure it's visible
            driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
            time.sleep(2)
            
            # Click the button
            print("Clicking Download All button...")
            download_button.click()
            
            # Wait for download to start
            print("Waiting for download to start...")
            time.sleep(15)  # Give time for download to complete
            
            # Check if any files were downloaded
            downloaded_files = []
            if os.path.exists(download_dir):
                for filename in os.listdir(download_dir):
                    if filename.endswith('.zip') or filename.endswith('.ZIP'):
                        downloaded_files.append(filename)
                        print(f"SUCCESS: Downloaded zip file: {filename}")
            
            return downloaded_files
            
        else:
            print("ERROR: Could not find 'Download All' button")
            # Let's see what buttons/links are available
            print("\nAvailable buttons and links:")
            buttons = driver.find_elements(By.TAG_NAME, "button")
            for i, btn in enumerate(buttons[:10]):  # Show first 10
                try:
                    print(f"  Button {i+1}: '{btn.text}' (class: {btn.get_attribute('class')})")
                except:
                    pass
            
            links = driver.find_elements(By.TAG_NAME, "a")
            for i, link in enumerate(links[:10]):  # Show first 10
                try:
                    text = link.text.strip()
                    if text and ('download' in text.lower() or 'attachment' in text.lower()):
                        print(f"  Link {i+1}: '{text}' (href: {link.get_attribute('href')})")
                except:
                    pass
            
            return []
        
    except Exception as e:
        print(f"ERROR: Failed to click download button: {e}")
        return []
    finally:
        if driver:
            print("Closing browser...")
            driver.quit()

def extract_zip_file(zip_path, notice_id):
    """Extract a zip file and return list of extracted files."""
    import zipfile
    
    extracted_files = []
    extract_dir = os.path.join(DOWNLOADS_DIR, "extracted", notice_id)
    os.makedirs(extract_dir, exist_ok=True)
    
    try:
        print(f"Extracting zip file: {zip_path}")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
            extracted_files = zip_ref.namelist()
        
        print(f"SUCCESS: Extracted {len(extracted_files)} files from zip")
        for filename in extracted_files:
            print(f"  - {filename}")
        
        return extracted_files, extract_dir
        
    except Exception as e:
        print(f"ERROR: Failed to extract zip file: {e}")
        return [], extract_dir

PIEE_DEBUG_DIR = 'piee_debug'

# Max attempts to load the actual PIEE opportunity page (server sometimes returns index)
PIEE_LOAD_ATTEMPTS = 3
# Seconds to wait after page load for JSF to render
PIEE_PAGE_SETTLE_SEC = 8
# Seconds to wait between retries when we get the wrong page
PIEE_RETRY_DELAY_SEC = 4
# Retries for PIEE when download returns 0 files (caller may use piee_link in sheet)
PIEE_DOWNLOAD_ATTEMPTS = 3


def _is_piee_wrong_page(driver):
    """
    Return True if the current page is the PIEE home/index page instead of
    the opportunity detail page. The server sometimes returns the index
    (system messages / "Welcome to the Solicitation Module") instead of
    the actual solicitation with attachments.
    """
    try:
        ps = driver.page_source
        if "Welcome to the Solicitation Module" in ps:
            return True
        if 'action="/sol/xhtml/unauth/index.xhtml"' in ps or "action='/sol/xhtml/unauth/index.xhtml'" in ps:
            return True
        if "oppMgmtLink.xhtml" not in ps:
            return True
        return False
    except Exception:
        return True


def _save_piee_page_snapshot(driver, notice_id, label="snapshot"):
    """
    Save the current browser state (page source HTML + screenshot) for a PIEE page.

    Files are written to PIEE_DEBUG_DIR/<notice_id>/:
      - <label>.html   – full page source
      - <label>.png    – screenshot

    Returns a dict with keys 'html_path' and 'screenshot_path' (or None on failure).
    """
    debug_dir = os.path.join(PIEE_DEBUG_DIR, notice_id)
    os.makedirs(debug_dir, exist_ok=True)

    result = {"html_path": None, "screenshot_path": None}

    try:
        html_path = os.path.join(debug_dir, f"{label}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        result["html_path"] = html_path
        print(f"  💾 Page source saved → {html_path} ({os.path.getsize(html_path):,} bytes)")
    except Exception as e:
        print(f"  ⚠️  Could not save page source: {e}")

    try:
        screenshot_path = os.path.join(debug_dir, f"{label}.png")
        driver.save_screenshot(screenshot_path)
        result["screenshot_path"] = screenshot_path
        print(f"  📸 Screenshot saved  → {screenshot_path}")
    except Exception as e:
        print(f"  ⚠️  Could not save screenshot: {e}")

    return result


def download_files_from_piee_url(piee_url, notice_id=None):
    """
    Use Selenium to download files from a PIEE solicitation page.

    PIEE uses JavaServer Faces (JSF) form submissions, so plain HTTP POST requests
    will not work — a real browser is required to trigger the download.

    If notice_id is not provided, it is extracted from the URL (noticeId=...).
    The server sometimes returns the index/home page instead of the opportunity;
    we retry up to PIEE_LOAD_ATTEMPTS times when the wrong page is detected.

    After the page loads the full HTML source and a screenshot are saved to
    PIEE_DEBUG_DIR/<notice_id>/initial_load.{html,png} so you can inspect what
    the page actually returned.  A second snapshot is taken after any modal
    dismissal (post_modal.{html,png}).

    Returns a list of downloaded filenames inside the notice_id download directory.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys
    import time

    if not notice_id:
        match = re.search(r'noticeId=([A-Z0-9\-]+)', piee_url, re.IGNORECASE)
        notice_id = match.group(1) if match else "unknown"
    print(f"Downloading files from PIEE URL: {piee_url} (notice_id={notice_id})")

    download_dir = os.path.abspath(os.path.join(DOWNLOADS_DIR, notice_id))
    os.makedirs(download_dir, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    chrome_options.add_experimental_option("prefs", prefs)

    driver = None
    try:
        for attempt in range(1, PIEE_LOAD_ATTEMPTS + 1):
            print(f"PIEE load attempt {attempt}/{PIEE_LOAD_ATTEMPTS}...")
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
            driver.set_page_load_timeout(60)
            driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": download_dir,
            })

            print("Loading PIEE page...")
            driver.get(piee_url)
            time.sleep(PIEE_PAGE_SETTLE_SEC)

            # --- Save initial page state so we can inspect what the server returned ---
            print("Saving initial PIEE page snapshot...")
            _save_piee_page_snapshot(driver, notice_id, label="initial_load")

            if _is_piee_wrong_page(driver):
                print("  WARNING: Got PIEE index/home page instead of opportunity; retrying...")
                driver.quit()
                driver = None
                if attempt < PIEE_LOAD_ATTEMPTS:
                    time.sleep(PIEE_RETRY_DELAY_SEC)
                continue

            # Optional: wait for the download button or attachment link to be present
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "downloadAllAttachments"))
                )
            except Exception:
                try:
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, "//table//a[contains(@onclick, 'attachmentIndex')]"))
                    )
                except Exception:
                    pass
            break

        if not driver:
            print("ERROR: All PIEE load attempts returned the index page; cannot download.")
            return []

        # Dismiss session-expired or other overlay modals
        modal_dismissed = False
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(1)
        except Exception:
            pass

        for modal_selector in [
            "#piee-session-modal .close",
            ".modal .btn-primary",
            ".modal .close",
            "[data-dismiss='modal']",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, modal_selector)
                btn.click()
                time.sleep(0.5)
                modal_dismissed = True
                break
            except Exception:
                pass

        if modal_dismissed:
            print("Saving post-modal PIEE page snapshot...")
            _save_piee_page_snapshot(driver, notice_id, label="post_modal")

        # ------------------------------------------------------------------ #
        # Strategy 1: "Download All Attachments" via its known element ID.    #
        # The page renders this as an <a id="downloadAllAttachments"> whose   #
        # onclick fires myfaces.oam.submitForm — bypass checkAltFF() entirely  #
        # by calling the JSF submit directly from JS.                          #
        # ------------------------------------------------------------------ #
        print("Trying 'Download All Attachments' via JSF form submit (id=downloadAllAttachments)...")
        try:
            # Confirm the element is present before triggering the submit
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "downloadAllAttachments"))
            )
            driver.execute_script("myfaces.oam.submitForm('form', 'downloadAllAttachments');")
            print("Triggered JSF 'Download All Attachments' submit, polling for download...")
            for _ in range(15):  # up to 45 seconds
                time.sleep(3)
                downloaded = [
                    f for f in os.listdir(download_dir)
                    if not f.endswith(".crdownload")
                ]
                if downloaded:
                    print(f"SUCCESS: Downloaded {len(downloaded)} file(s) via Download All: {downloaded}")
                    return downloaded
            print("WARNING: No files after 45s from Download All, falling back to per-file download...")
        except Exception as e:
            print(f"  Download All submit failed ({e}), falling back to per-file download...")

        # ------------------------------------------------------------------ #
        # Strategy 2: Submit each attachment individually by attachmentIndex.  #
        # Parse the onclick attributes to discover how many attachments exist  #
        # then call myfaces.oam.submitForm once per index.                     #
        # ------------------------------------------------------------------ #
        print("Attempting per-file JSF form submissions via attachmentIndex...")
        indices = re.findall(r"\['attachmentIndex','(\d+)'\]", driver.page_source)
        if not indices:
            # Broader fallback: look for the numeric suffix in the button IDs
            indices = re.findall(r"j_id_4s:(\d+):j_id_4v", driver.page_source)
        indices = sorted(set(indices), key=int)
        print(f"  Found {len(indices)} attachment(s) to download: indices {indices}")

        for idx in indices:
            btn_id = f"j_id_4s:{idx}:j_id_4v"
            print(f"  Submitting attachmentIndex={idx} (btn_id={btn_id})...")
            try:
                driver.execute_script(
                    "myfaces.oam.submitForm('form', arguments[0], null, [['attachmentIndex', arguments[1]]]);",
                    btn_id, str(idx)
                )
                # Poll for a new file appearing (up to 20 s per file)
                files_before = set(os.listdir(download_dir))
                for _ in range(7):
                    time.sleep(3)
                    files_now = {
                        f for f in os.listdir(download_dir)
                        if not f.endswith(".crdownload")
                    }
                    new_files = files_now - files_before
                    if new_files:
                        print(f"  ✅ New file(s) appeared: {new_files}")
                        break
            except Exception as e:
                print(f"  Error submitting index {idx}: {e}")

        downloaded = [
            f for f in os.listdir(download_dir)
            if not f.endswith(".crdownload")
        ]
        if downloaded:
            print(f"SUCCESS: Downloaded {len(downloaded)} file(s) via per-file submit: {downloaded}")
            return downloaded

        # ------------------------------------------------------------------ #
        # Strategy 3: Last-resort — click every attachment link in the table. #
        # ------------------------------------------------------------------ #
        print("Last resort: clicking attachment links in the table directly...")
        attachment_links = driver.find_elements(
            By.XPATH,
            "//table//a[contains(@onclick, 'attachmentIndex')]"
        )
        for link in attachment_links:
            try:
                label = link.text.strip()
                print(f"  Clicking: '{label}'")
                driver.execute_script("arguments[0].click();", link)
                time.sleep(8)
            except Exception as e:
                print(f"  Error clicking attachment link: {e}")

        downloaded = [
            f for f in os.listdir(download_dir)
            if not f.endswith(".crdownload")
        ]
        if downloaded:
            print(f"SUCCESS: Downloaded {len(downloaded)} file(s) via link clicks: {downloaded}")
            return downloaded

        print("ERROR: No files downloaded from PIEE page.")
        return []

    except Exception as e:
        print(f"ERROR: Failed to download from PIEE: {e}")
        return []
    finally:
        if driver:
            print("Closing PIEE browser...")
            driver.quit()


def download_files_from_piee_via_sam(sam_url, piee_sol_number, notice_id):
    """
    Navigate to SAM.gov, click the PIEE link on the page, and download
    files from the resulting PIEE page.  This avoids directly navigating
    to the PIEE URL which often gets blocked/redirected by the PIEE server
    because there is no referrer or session from SAM.gov.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys
    import time

    print(f"Attempting PIEE download via SAM.gov click-through for {notice_id}")

    download_dir = os.path.abspath(os.path.join(DOWNLOADS_DIR, notice_id))
    os.makedirs(download_dir, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    chrome_options.add_experimental_option("prefs", prefs)

    driver = None
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )
        driver.set_page_load_timeout(60)
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": download_dir,
        })

        # Step 1: Load the SAM.gov page
        print(f"Loading SAM.gov page: {sam_url}")
        driver.get(sam_url)
        time.sleep(12)

        # Step 2: Find and click the PIEE link on the SAM.gov page
        piee_link = None

        for xpath in [
            "//a[contains(@href, 'piee.eb.mil')]",
            "//a[contains(text(), 'PIEE')]",
            "//a[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'piee')]",
            "//a[contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'piee')]",
        ]:
            try:
                piee_link = WebDriverWait(driver, 8).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                print(f"Found PIEE link via xpath {xpath}: href={piee_link.get_attribute('href')}")
                break
            except Exception:
                continue

        if not piee_link:
            print("Could not find PIEE link on SAM.gov page")
            _save_piee_page_snapshot(driver, notice_id, label="sam_no_piee_link")
            return []

        # Step 3: Click the PIEE link, tracking window handles for new-tab detection
        original_window = driver.current_window_handle
        original_handles = set(driver.window_handles)

        print("Clicking PIEE link on SAM.gov page...")
        driver.execute_script("arguments[0].scrollIntoView(true);", piee_link)
        time.sleep(1)
        piee_link.click()
        time.sleep(3)

        # Step 4: Switch to new tab if one opened
        new_handles = set(driver.window_handles) - original_handles
        if new_handles:
            new_tab = new_handles.pop()
            driver.switch_to.window(new_tab)
            print(f"Switched to new PIEE tab: {driver.current_url}")
        else:
            print(f"No new tab; current URL: {driver.current_url}")

        # Step 5: Wait for PIEE page to settle
        time.sleep(4)
        _save_piee_page_snapshot(driver, notice_id, label="piee_via_sam_click")

        if _is_piee_wrong_page(driver):
            print("WARNING: Got PIEE index page even via SAM.gov click-through")
            return []

        # Step 6: Dismiss modals
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(1)
        except Exception:
            pass

        for modal_selector in [
            "#piee-session-modal .close",
            ".modal .btn-primary",
            ".modal .close",
            "[data-dismiss='modal']",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, modal_selector)
                btn.click()
                time.sleep(0.5)
                break
            except Exception:
                pass

        # Step 7: Download All Attachments via JSF form submit
        print("Trying 'Download All Attachments' via JSF form submit...")
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "downloadAllAttachments"))
            )
            driver.execute_script(
                "myfaces.oam.submitForm('form', 'downloadAllAttachments');"
            )
            print("Triggered JSF submit, polling for download...")
            for _ in range(15):
                time.sleep(3)
                downloaded = [
                    f for f in os.listdir(download_dir)
                    if not f.endswith(".crdownload")
                ]
                if downloaded:
                    print(f"SUCCESS: Downloaded {len(downloaded)} file(s) via Download All: {downloaded}")
                    return downloaded
            print("No files after 45s from Download All, trying per-file...")
        except Exception as e:
            print(f"  Download All failed ({e}), trying per-file...")

        # Step 8: Per-file download via attachmentIndex
        print("Attempting per-file JSF form submissions...")
        indices = re.findall(r"\['attachmentIndex','(\d+)'\]", driver.page_source)
        if not indices:
            indices = re.findall(r"j_id_4s:(\d+):j_id_4v", driver.page_source)
        indices = sorted(set(indices), key=int)
        print(f"  Found {len(indices)} attachment(s): indices {indices}")

        for idx in indices:
            btn_id = f"j_id_4s:{idx}:j_id_4v"
            try:
                driver.execute_script(
                    "myfaces.oam.submitForm('form', arguments[0], null, "
                    "[['attachmentIndex', arguments[1]]]);",
                    btn_id, str(idx),
                )
                files_before = set(os.listdir(download_dir))
                for _ in range(7):
                    time.sleep(3)
                    files_now = {
                        f for f in os.listdir(download_dir)
                        if not f.endswith(".crdownload")
                    }
                    if files_now - files_before:
                        print(f"  ✅ New file(s): {files_now - files_before}")
                        break
            except Exception as e:
                print(f"  Error submitting index {idx}: {e}")

        # Step 9: Last resort — click attachment links directly
        attachment_links = driver.find_elements(
            By.XPATH, "//table//a[contains(@onclick, 'attachmentIndex')]"
        )
        if attachment_links and not os.listdir(download_dir):
            print("Last resort: clicking attachment links directly...")
            for link in attachment_links:
                try:
                    driver.execute_script("arguments[0].click();", link)
                    time.sleep(8)
                except Exception:
                    pass

        downloaded = [
            f for f in os.listdir(download_dir)
            if not f.endswith(".crdownload")
        ]
        if downloaded:
            print(f"SUCCESS: Downloaded {len(downloaded)} file(s) via SAM.gov click-through")
        else:
            print("No files downloaded from PIEE via SAM.gov click-through")
        return downloaded

    except Exception as e:
        print(f"ERROR: PIEE download via SAM.gov click-through failed: {e}")
        return []
    finally:
        if driver:
            driver.quit()


def _detect_piee_solicitation_number(ui_text):
    """
    Detect a PIEE solicitation link in SAM.gov UI text and return
    the solicitation number used to construct the PIEE URL.

    Returns the solicitation number string, or None if not found.
    """
    # Most reliable: "PIEE Solicitation Module Link for W912ES26BA004"
    match = re.search(r'PIEE Solicitation Module Link for ([A-Z0-9\-]+)', ui_text)
    if match:
        return match.group(1)

    # Broader: look for the PIEE URL already in the text
    match = re.search(r'piee\.eb\.mil[^\s]*noticeId=([A-Z0-9\-]+)', ui_text, re.IGNORECASE)
    if match:
        return match.group(1)

    # If text just mentions PIEE at all (less reliable; caller can use notice_id as fallback)
    if re.search(r'\bpiee\b', ui_text, re.IGNORECASE):
        return None  # Return None; caller decides whether to use notice_id

    return None


def download_files_from_sam_url(sam_url, resource_links=None, notice_id=None):
    """
    Download files from SAM.gov and extract text content.

    Supports three download methods:
    1. API-based: Uses resourceLinks directly (faster, preferred)
    2. PIEE Selenium: Detects PIEE solicitation links and downloads via piee.eb.mil (retries up to PIEE_DOWNLOAD_ATTEMPTS)
    3. SAM.gov Selenium: Falls back to clicking 'Download All' button on SAM.gov

    Parameters:
    - sam_url: The SAM.gov opportunity URL
    - resource_links: Optional list of direct download URLs from the API
    - notice_id: Optional notice ID (extracted from URL if not provided)

    Returns:
    - (content, extra): tuple. content is the complete text or a skip message string.
      extra is a dict; when PIEE was used and no files could be extracted after retries,
      extra['piee_link'] is the PIEE URL for the spreadsheet.
    """
    print(f"Processing SAM.gov URL: {sam_url}")
    
    # Extract notice ID for naming if not provided
    if not notice_id:
        match = re.search(r'/opp/([^/]+)/view', sam_url)
        notice_id = match.group(1) if match else "unknown"
    print(f"Notice ID: {notice_id}")
    
    # First, get the UI text from the page
    print("Fetching UI text from the page...")
    ui_data = fetch_ui_link_data(sam_url, use_cache=False)
    ui_text = ""
    if ui_data and 'text_content' in ui_data:
        ui_text = ui_data['text_content']
        print(f"SUCCESS: Retrieved {len(ui_text)} characters of UI text")
    else:
        print("WARNING: Could not retrieve UI text")
    
    # Create directories
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(EXTRACTED_TEXT_DIR, exist_ok=True)
    
    # Determine download method
    downloaded_files = []
    use_api = USE_API_DOWNLOAD and resource_links and len(resource_links) > 0
    
    if use_api:
        # Method 1: API-based download (preferred - faster and more reliable)
        print(f"\n🚀 Using API-based download ({len(resource_links)} resource links available)")
        downloaded_files = download_files_from_resource_links(resource_links, notice_id)
        
        if downloaded_files:
            # Extract text from downloaded files
            attachment_text = extract_text_from_downloaded_files(downloaded_files, notice_id)
            
            # Build complete text
            complete_text = ui_text
            if attachment_text:
                complete_text += "\n\n" + "="*100 + "\n" + "ATTACHMENT FILES (API Download)\n" + "="*100 + "\n\n"
                complete_text += attachment_text
        else:
            print("API download returned no files, falling back to Selenium...")
            use_api = False
    
    if not use_api:
        # Detect whether this is a PIEE solicitation before using SAM.gov Selenium
        piee_sol_number = _detect_piee_solicitation_number(ui_text)

        if piee_sol_number:
            piee_url = (
                f"https://piee.eb.mil/sol/xhtml/unauth/search/oppMgmtLink.xhtml"
                f"?noticeId={piee_sol_number}&noticeType=SolicitationNotice"
            )
            print(f"\nPIEE solicitation detected (sol# {piee_sol_number})")

            # Method 2a-i: Click the PIEE link from within SAM.gov (preserves referrer/session)
            print("Trying PIEE download via SAM.gov click-through...")
            zip_files = download_files_from_piee_via_sam(sam_url, piee_sol_number, notice_id)

            # Method 2a-ii: Fallback to direct PIEE URL navigation
            if not zip_files:
                print("SAM.gov click-through failed, falling back to direct PIEE URL...")
                for attempt in range(1, PIEE_DOWNLOAD_ATTEMPTS + 1):
                    zip_files = download_files_from_piee_url(piee_url, notice_id)
                    if zip_files:
                        break
                    print(f"PIEE direct attempt {attempt}/{PIEE_DOWNLOAD_ATTEMPTS} returned no files, retrying...")

            if not zip_files:
                print("No files from PIEE after all attempts; caller will use PIEE link in spreadsheet.")
                return (ui_text, {"piee_link": piee_url})
            download_label = "PIEE Download"
        else:
            # Method 2b: SAM.gov Selenium download (original fallback)
            print(f"\nUsing Selenium-based download (clicking 'Download All' button)")
            zip_files = click_download_all_button(sam_url, notice_id)
            download_label = "Selenium Download"

        if not zip_files:
            print("No files were downloaded")
            return (ui_text, {})
        
        # Build the complete text string: UI text first, then files
        complete_text = ui_text + "\n\n" + "="*100 + f"\n" + f"ATTACHMENT FILES ({download_label})\n" + "="*100 + "\n\n"
        
        all_extracted_files = []

        def _process_file_into_text(file_path, filename):
            """Read a single file and append its text to complete_text. Returns extracted text or ''."""
            nonlocal complete_text
            try:
                if os.path.isdir(file_path):
                    return ""
                print(f"\nProcessing file: {filename}")
                with open(file_path, 'rb') as f:
                    file_content = f.read()
                print(f"File size: {len(file_content)} bytes")
                all_extracted_files.append(filename)
                downloaded_files.append(file_path)
                extracted_text = extract_text_from_file_content(filename, file_content)
                complete_text += f"\nFILENAME: {filename}\n"
                complete_text += "-" * 50 + "\n"
                if extracted_text:
                    print(f"Raw extracted text: {len(extracted_text)} characters")
                    complete_text += extracted_text + "\n\n"
                    print(f"SUCCESS: Extracted {len(extracted_text)} characters")
                    return extracted_text
                else:
                    complete_text += "[No text content - binary file or unsupported format]\n\n"
                    print(f"No text extracted from {filename}")
                    return ""
            except Exception as e:
                print(f"ERROR processing {filename}: {e}")
                complete_text += f"[ERROR processing file: {e}]\n\n"
                return ""

        # Process each downloaded file; handle both ZIPs and plain files
        for downloaded_filename in zip_files:
            file_path = os.path.join(DOWNLOADS_DIR, notice_id, downloaded_filename)

            if downloaded_filename.lower().endswith('.zip'):
                # Extract zip and process contents
                extracted_files, extract_dir = extract_zip_file(file_path, notice_id)
                for filename in extracted_files:
                    _process_file_into_text(os.path.join(extract_dir, filename), filename)
            else:
                # Plain file (PDF, DOC, etc.) — process directly
                _process_file_into_text(file_path, downloaded_filename)

        print(f"Extracted files: {all_extracted_files}")
    
    # Check for mandatory site visit and controlled attachments before any further processing
    print("\nChecking for site visit and controlled attachment requirements...")
    site_visit_result = has_site_visit(complete_text)
    print(f"  Site visit detected: {site_visit_result['has_site_visit']}")
    print(f"  Controlled attachments detected: {site_visit_result['has_controlled_attachments']}")
    print(f"  Reasoning: {site_visit_result['reasoning']}")
    if site_visit_result["has_site_visit"]:
        print("Skipping: Contract has a site visit/pre-proposal conference.")
        return ("contract requires mandatory site visit", {})
    if site_visit_result["has_controlled_attachments"]:
        print("Skipping: Contract has controlled (non-public) attachments.")
        return ("contract has controlled attachments", {})

    # Apply filtering at the end if enabled
    final_text = complete_text
    if ENABLE_CONTENT_FILTERING:
        print(f"\nApplying vendor-focused filtering to complete text ({len(complete_text)} characters)...")
        final_text = filter_vendor_relevant_content(complete_text)
        print(f"Filtered complete text: {len(complete_text)} -> {len(final_text)} characters")
    
    # Save the complete text to a file
    output_file = os.path.join(EXTRACTED_TEXT_DIR, f"{notice_id}_COMPLETE_TEXT.txt")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(final_text)
    
    # If filtering was enabled, also save a debug version with filtering status
    if ENABLE_CONTENT_FILTERING:
        debug_file = os.path.join(EXTRACTED_TEXT_DIR, f"{notice_id}_FILTERED_DEBUG.txt")
        with open(debug_file, 'w', encoding='utf-8') as f:
            f.write(f"VENDOR-FOCUSED FILTERING ENABLED - This file contains Gemini-filtered content\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n")
            f.write(f"Original characters: {len(complete_text)}\n")
            f.write(f"Filtered characters: {len(final_text)}\n")
            f.write(f"Download method: {'API' if use_api else 'Selenium'}\n")
            f.write("="*80 + "\n\n")
            f.write(final_text)
    
    download_method = "API" if use_api else "Selenium"
    print(f"\n=== COMPLETE ({download_method} download) ===")
    print(f"Downloaded files: {len(downloaded_files)}")
    print(f"Complete text saved to: {output_file}")
    print(f"Total characters in final text: {len(final_text)}")
    
    return (final_text, {})

def cleanup_old_downloads(keep_notice_ids=None):
    """Clean up old download directories, optionally keeping specific notice IDs."""
    if not os.path.exists(DOWNLOADS_DIR):
        return
    
    keep_notice_ids = keep_notice_ids or []
    
    for item in os.listdir(DOWNLOADS_DIR):
        item_path = os.path.join(DOWNLOADS_DIR, item)
        
        # Skip if it's not a directory
        if not os.path.isdir(item_path):
            continue
        
        # Skip if it's in the keep list
        if item in keep_notice_ids:
            print(f"Keeping directory: {item}")
            continue
        
        # Skip the general 'extracted' folder if it exists
        if item == "extracted":
            # Clean up the extracted folder's subdirectories
            extracted_path = os.path.join(DOWNLOADS_DIR, "extracted")
            if os.path.exists(extracted_path):
                for sub_item in os.listdir(extracted_path):
                    if sub_item not in keep_notice_ids:
                        sub_path = os.path.join(extracted_path, sub_item)
                        if os.path.isdir(sub_path):
                            print(f"Removing old extracted directory: {sub_path}")
                            import shutil
                            shutil.rmtree(sub_path)
            continue
        
        # Remove old notice directories
        print(f"Removing old download directory: {item_path}")
        import shutil
        shutil.rmtree(item_path)

def main():
    # Change this URL to any SAM.gov contract URL you want
    sam_url = "https://sam.gov/workspace/contract/opp/1841aa4d39b843adac14f93d6491f60d/view"
    
    # Get the complete text string
    complete_text = fetch_ui_link_data(sam_url)
    parsed_data = parse_sam_ui_metadata(complete_text['text_content'])
    print(parsed_data)
    
    # Print the complete text (you can also return it or use it however you need)
    # print("\n" + "="*100)
    # print("COMPLETE TEXT OUTPUT")
    # print("="*100)
    # print(complete_text)
    
    return complete_text

def test_piee_links(runs_per_link=2):
    """
    Test PIEE file extraction with the given links, multiple runs per link.
    Returns a list of (url, run_index, success, file_count_or_error).
    """
    test_urls = [
        "https://piee.eb.mil/sol/xhtml/unauth/search/oppMgmtLink.xhtml?noticeId=W911WN26QA034&noticeType=CombinedSynopsisSolicitation",
        "https://piee.eb.mil/sol/xhtml/unauth/search/oppMgmtLink.xhtml?noticeId=W911S226U2798&noticeType=CombinedSynopsisSolicitation",
        "https://piee.eb.mil/sol/xhtml/unauth/search/oppMgmtLink.xhtml?noticeId=W911SA26QA086&noticeType=CombinedSynopsisSolicitation",
    ]
    results = []
    for url in test_urls:
        notice_id = re.search(r'noticeId=([A-Z0-9\-]+)', url, re.IGNORECASE)
        notice_id = notice_id.group(1) if notice_id else "unknown"
        for run in range(1, runs_per_link + 1):
            print("\n" + "=" * 80)
            print(f"TEST: {notice_id} — run {run}/{runs_per_link}")
            print("=" * 80)
            try:
                files = download_files_from_piee_url(url, notice_id=notice_id)
                success = len(files) > 0
                results.append((url, run, success, len(files) if success else "0 files"))
                print(f"  → {'PASS' if success else 'FAIL'}: {len(files)} file(s)")
            except Exception as e:
                results.append((url, run, False, str(e)))
                print(f"  → FAIL: {e}")
    return results


if __name__ == "__main__":
    import sys
    if "--test-piee" in sys.argv or "test_piee" in str(sys.argv):
        runs = 2
        for i, arg in enumerate(sys.argv):
            if arg == "--runs" and i + 1 < len(sys.argv):
                runs = int(sys.argv[i + 1])
                break
        print("Running PIEE extraction test (3 links, {} runs each)...".format(runs))
        results = test_piee_links(runs_per_link=runs)
        passed = sum(1 for r in results if r[2])
        total = len(results)
        print("\n" + "=" * 80)
        print("PIEE TEST SUMMARY: {} / {} runs succeeded".format(passed, total))
        print("=" * 80)
        for url, run, success, detail in results:
            notice = re.search(r'noticeId=([A-Z0-9\-]+)', url, re.IGNORECASE)
            notice = notice.group(1) if notice else "?"
            print("  {} run {}: {} — {}".format(notice, run, "PASS" if success else "FAIL", detail))
    else:
        download_files_from_piee_url(
            "https://piee.eb.mil/sol/xhtml/unauth/search/oppMgmtLink.xhtml?noticeId=W912ES26BA004&noticeType=SolicitationNotice",
            "werd"
        )
