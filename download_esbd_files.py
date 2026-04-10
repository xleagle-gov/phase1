#!/usr/bin/env python3
"""
ESBD File Downloader
Downloads all attachment files from Texas SmartBuy ESBD contract pages.

Usage:
    from download_esbd_files import download_esbd_files
    
    # Download files from any ESBD URL
    files = download_esbd_files("https://www.txsmartbuy.gov/esbd/696-TC-25-P024")
"""

import os
import re
import requests
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin, urlparse
from datetime import datetime
import zipfile
import io
from google_drive_utils import extract_text_from_file_content

# Configuration
DOWNLOADS_DIR = 'esbd_downloads'
MAX_RETRIES = 3
RETRY_DELAY = 2

def setup_driver(download_dir):
    """Set up Chrome driver with download preferences."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Set download preferences
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True  # Download PDFs instead of opening in browser
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(60)
    
    return driver

def extract_contract_id_from_url(url):
    """Extract contract ID from ESBD URL."""
    # Pattern: https://www.txsmartbuy.gov/esbd/696-TC-25-P024
    match = re.search(r'/esbd/([^/\?]+)', url)
    if match:
        contract_id = match.group(1)
        # Clean up any query parameters or fragments
        contract_id = contract_id.split('?')[0].split('#')[0]
        return contract_id
    
    # Fallback: extract contract-like patterns from any part of the URL
    # Common patterns: XXX-XX-XX-XXXX, XXX-XXX-XX-XXXX, etc.
    patterns = [
        r'(\d{3}-[A-Z]+-\d{2}-[A-Z]\d{3})',  # 696-TC-25-P024
        r'(\d{3}-[A-Z]{2,4}-\d{2}-[A-Z]\d{3,4})',  # Similar variations
        r'([A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)',  # Generic alphanumeric pattern
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    # Last resort: use timestamp
    from datetime import datetime
    return f"esbd_contract_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def wait_for_downloads(download_dir, expected_count, initial_files, timeout=60):
    """Wait for downloads to complete, tracking only new files."""
    print(f"Waiting for {expected_count} files to download...")
    start_time = time.time()
    initial_file_set = set(initial_files)
    
    while time.time() - start_time < timeout:
        # Check for completed downloads (non-temp files)
        current_files = [f for f in os.listdir(download_dir) if not f.endswith('.crdownload') and not f.endswith('.tmp')]
        current_file_set = set(current_files)
        
        # Only count new files downloaded in this session
        new_files = current_file_set - initial_file_set
        
        if len(new_files) >= expected_count:
            print(f"SUCCESS: Downloaded {len(new_files)} new files")
            return list(new_files)
        
        print(f"Progress: {len(new_files)}/{expected_count} files downloaded", end='\r')
        time.sleep(2)
    
    # Final check
    current_files = [f for f in os.listdir(download_dir) if not f.endswith('.crdownload') and not f.endswith('.tmp')]
    current_file_set = set(current_files)
    new_files = current_file_set - initial_file_set
    
    print(f"\nTimeout reached. Downloaded {len(new_files)} new files after {timeout} seconds")
    return list(new_files)

def download_files_with_requests(attachment_links, download_dir, contract_id):
    """Fallback method: Download files directly using requests."""
    print("Attempting direct download using requests...")
    downloaded_files = []
    
    for i, link_info in enumerate(attachment_links):
        filename = link_info.get('filename', f"{contract_id}_attachment_{i+1}.pdf")
        download_url = link_info.get('url')
        
        if not download_url:
            print(f"Warning: No download URL found for {filename}")
            continue
        
        try:
            print(f"Downloading {filename}...")
            
            # Add headers to mimic browser request
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            response = requests.get(download_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Clean filename
            safe_filename = re.sub(r'[^\w\-_\.]', '_', filename)
            file_path = os.path.join(download_dir, safe_filename)
            
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            file_size = len(response.content)
            print(f"SUCCESS: Downloaded {safe_filename} ({file_size} bytes)")
            downloaded_files.append(safe_filename)
            
        except Exception as e:
            print(f"ERROR downloading {filename}: {e}")
    
    return downloaded_files

# Text extraction is now handled by the imported function from google_drive_utils

def extract_zip_file(zip_path, contract_id):
    """Extract a zip file and return list of extracted files with their paths."""
    extracted_files = []
    extract_dir = os.path.join(DOWNLOADS_DIR, contract_id, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    
    try:
        print(f"Extracting zip file: {os.path.basename(zip_path)}")
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

def process_downloaded_files(downloaded_files, download_dir, contract_id):
    """Process downloaded files and extract text from them."""
    print(f"\nProcessing {len(downloaded_files)} downloaded files for text extraction...")
    
    complete_text = f"ESBD CONTRACT: {contract_id}\n"
    complete_text += f"DOWNLOADED FILES TEXT EXTRACTION\n"
    complete_text += "=" * 80 + "\n\n"
    
    all_processed_files = []
    
    for filename in downloaded_files:
        file_path = os.path.join(download_dir, filename)
        
        if not os.path.exists(file_path):
            print(f"Warning: File not found: {filename}")
            continue
        
        print(f"\nProcessing file: {filename}")
        
        try:
            # Read file content
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            file_size = len(file_content)
            print(f"File size: {file_size} bytes")
            
            # Handle zip files specially
            if filename.lower().endswith('.zip'):
                print(f"Processing ZIP file: {filename}")
                extracted_files, extract_dir = extract_zip_file(file_path, contract_id)
                
                if extracted_files:
                    complete_text += f"\nZIP FILE: {filename}\n"
                    complete_text += "-" * 50 + "\n"
                    complete_text += f"Contains {len(extracted_files)} files:\n"
                    
                    for extracted_filename in extracted_files:
                        extracted_path = os.path.join(extract_dir, extracted_filename)
                        
                        # Skip directories
                        if os.path.isdir(extracted_path):
                            continue
                        
                        try:
                            print(f"  Processing extracted file: {extracted_filename}")
                            
                            with open(extracted_path, 'rb') as ef:
                                extracted_content = ef.read()
                            
                            # Extract text from the extracted file
                            extracted_text = extract_text_from_file_content(extracted_filename, extracted_content)
                            
                            complete_text += f"\n  EXTRACTED FILE: {extracted_filename}\n"
                            complete_text += "  " + "-" * 40 + "\n"
                            
                            if extracted_text:
                                complete_text += extracted_text + "\n\n"
                                print(f"    Extracted {len(extracted_text)} characters of text")
                            else:
                                complete_text += "  [No text content extracted]\n\n"
                                print(f"    No text extracted from {extracted_filename}")
                            
                            all_processed_files.append(f"{filename}:{extracted_filename}")
                            
                        except Exception as e:
                            print(f"    ERROR processing extracted file {extracted_filename}: {e}")
                            complete_text += f"  [ERROR processing {extracted_filename}: {e}]\n\n"
                else:
                    complete_text += f"[Could not extract files from {filename}]\n\n"
            
            else:
                # Regular file processing
                extracted_text = extract_text_from_file_content(filename, file_content)
                
                complete_text += f"\nFILE: {filename}\n"
                complete_text += "-" * 50 + "\n"
                
                if extracted_text:
                    complete_text += extracted_text + "\n\n"
                    print(f"Extracted {len(extracted_text)} characters of text")
                else:
                    complete_text += "[No text content extracted]\n\n"
                    print(f"No text extracted from {filename}")
                
                all_processed_files.append(filename)
        
        except Exception as e:
            print(f"ERROR processing {filename}: {e}")
            complete_text += f"[ERROR processing {filename}: {e}]\n\n"
    
    print(f"\nText extraction complete. Processed {len(all_processed_files)} files.")
    print(f"Total extracted text length: {len(complete_text)} characters")
    
    return complete_text

def download_esbd_files(esbd_url, extract_text=True):
    """Main function to download all files from an ESBD contract page."""
    print(f"Processing ESBD URL: {esbd_url}")
    
    # Extract contract ID
    contract_id = extract_contract_id_from_url(esbd_url)
    print(f"Contract ID: {contract_id}")
    
    # Create download directory
    download_dir = os.path.abspath(os.path.join(DOWNLOADS_DIR, contract_id))
    os.makedirs(download_dir, exist_ok=True)
    print(f"Download directory: {download_dir}")
    
    # Capture initial files in directory (to track only new downloads)
    initial_files = []
    if os.path.exists(download_dir):
        initial_files = [f for f in os.listdir(download_dir) if not f.endswith('.crdownload') and not f.endswith('.tmp')]
    print(f"Initial files in directory: {len(initial_files)}")
    
    driver = None
    try:
        # Set up browser
        driver = setup_driver(download_dir)
        
        # Load the ESBD page
        print("Loading ESBD page...")
        driver.get(esbd_url)
        
        # Wait for page to load
        print("Waiting for page to load...")
        time.sleep(5)
        
        # Look for attachment links in the page
        attachment_links = []
        
        # Method 1: Look for the attachments table and extract download links
        try:
            print("Looking for attachments table...")
            
            # Find the attachments table - look for table with "Attachments" header
            attachments_table = driver.find_elements(By.XPATH, "//table[.//th[contains(text(), 'Name')] or .//th[contains(text(), 'Description')]]")
            
            if not attachments_table:
                # Alternative: look for any table containing ESBD file names
                attachments_table = driver.find_elements(By.XPATH, "//table[.//td[contains(text(), 'ESBD_')]]")
            
            if attachments_table:
                print(f"Found {len(attachments_table)} potential attachment table(s)")
                table = attachments_table[0]  # Use the first table found
                
                # Find all rows in the table
                rows = table.find_elements(By.XPATH, ".//tr")
                print(f"Found {len(rows)} rows in attachments table")
                
                for i, row in enumerate(rows):
                    try:
                        # Skip header row
                        if i == 0:
                            continue
                        
                        # Get all cells in this row
                        cells = row.find_elements(By.XPATH, ".//td")
                        
                        if len(cells) >= 2:  # Should have at least name and description columns
                            # Look for filename in the first or second cell
                            filename = ""
                            for cell in cells[:2]:
                                cell_text = cell.text.strip()
                                # Accept any non-empty cell text that looks like a filename
                                if cell_text and ('.' in cell_text or len(cell_text) > 5):
                                    filename = cell_text
                                    break
                            
                            if filename:
                                print(f"Found attachment row: {filename}")
                                
                                # Look for download links in this row - check for 'X' links or download buttons
                                download_elements = row.find_elements(By.XPATH, ".//a[text()='X' or contains(@title, 'Download') or contains(@onclick, 'download')]")
                                
                                for element in download_elements:
                                    # Check for onclick or href attributes
                                    onclick = element.get_attribute('onclick')
                                    href = element.get_attribute('href')
                                    
                                    if onclick and 'download' in onclick.lower():
                                        # Extract download URL from onclick if present
                                        import re
                                        url_match = re.search(r"'([^']*)'", onclick)
                                        if url_match:
                                            download_url = url_match.group(1)
                                            # Make absolute URL if relative
                                            if download_url.startswith('/'):
                                                download_url = urljoin(esbd_url, download_url)
                                            
                                            attachment_links.append({
                                                'url': download_url,
                                                'filename': filename,
                                                'element': element
                                            })
                                            print(f"Found download link (onclick): {filename} -> {download_url}")
                                    
                                    elif href and href != '#':
                                        # Direct href link
                                        download_url = href
                                        if download_url.startswith('/'):
                                            download_url = urljoin(esbd_url, download_url)
                                        
                                        attachment_links.append({
                                            'url': download_url,
                                            'filename': filename,
                                            'element': element
                                        })
                                        print(f"Found download link (href): {filename} -> {download_url}")
                    
                    except Exception as e:
                        print(f"Error processing row {i}: {e}")
            
            else:
                print("No attachments table found")
        
        except Exception as e:
            print(f"Error finding attachments table: {e}")
        
        # Method 2: Look for filename elements (even without href)
        try:
            print("Looking for filename elements...")
            
            # Look for elements containing any filenames (with file extensions)
            filename_elements = driver.find_elements(By.XPATH, "//*[contains(text(), '.pdf') or contains(text(), '.doc') or contains(text(), '.xls') or contains(text(), '.txt') or contains(text(), '.zip')]")
            
            for element in filename_elements:
                filename = element.text.strip()
                # Accept any filename with a file extension
                if filename and '.' in filename:
                    print(f"Found filename element: {filename}")
                    
                    # Check if this element is clickable
                    tag_name = element.tag_name.lower()
                    
                    if tag_name == 'a' or element.get_attribute('onclick') or element.get_attribute('href'):
                        attachment_links.append({
                            'url': element.get_attribute('href'),  # May be None
                            'filename': filename,
                            'element': element,
                            'onclick': element.get_attribute('onclick')
                        })
                        print(f"Added clickable filename element: {filename}")
                    else:
                        # Look for a clickable parent or sibling
                        parent = element.find_element(By.XPATH, "..")
                        if parent.tag_name.lower() == 'a' or parent.get_attribute('onclick'):
                            attachment_links.append({
                                'url': parent.get_attribute('href'),
                                'filename': filename,
                                'element': parent,
                                'onclick': parent.get_attribute('onclick')
                            })
                            print(f"Added clickable parent for filename element: {filename}")
        
        except Exception as e:
            print(f"Error finding filename elements: {e}")
        
        # Method 2b: Look for any links with file patterns in href
        try:
            print("Looking for direct file links...")
            all_links = driver.find_elements(By.XPATH, "//a[@href]")
            
            for link in all_links:
                href = link.get_attribute('href')
                # Look for any file extension in the URL
                if href and ('.' in href and any(ext in href.lower() for ext in ['.pdf', '.doc', '.xls', '.txt', '.zip', '.docx', '.xlsx'])):
                    # Extract filename from URL
                    filename = os.path.basename(href)
                    if '?' in filename:
                        filename = filename.split('?')[0]
                    
                    # Only add if filename has an extension
                    if '.' in filename:
                        attachment_links.append({
                            'url': href,
                            'filename': filename,
                            'element': link
                        })
                        print(f"Found direct file link: {filename} -> {href}")
        
        except Exception as e:
            print(f"Error finding direct file links: {e}")
        
        # Method 3: Look for form submissions or JavaScript download functions
        try:
            print("Looking for JavaScript download functions...")
            
            # Look for elements with onclick handlers that might trigger downloads
            onclick_elements = driver.find_elements(By.XPATH, "//*[@onclick]")
            
            for element in onclick_elements:
                onclick = element.get_attribute('onclick')
                if onclick and 'download' in onclick.lower():
                    print(f"Found potential download onclick: {element.text} -> {onclick}")
                    
                    # Try to extract filename from onclick
                    import re
                    # Look for any filename pattern in onclick
                    filename_patterns = [
                        r'["\']([^"\']*\.[a-zA-Z]{2,5})["\']',  # Quoted filenames with extensions
                        r'([a-zA-Z0-9_-]+\.[a-zA-Z]{2,5})',     # Unquoted filenames with extensions
                    ]
                    
                    filename = None
                    for pattern in filename_patterns:
                        filename_match = re.search(pattern, onclick)
                        if filename_match:
                            filename = filename_match.group(1) if len(filename_match.groups()) > 0 else filename_match.group(0)
                            break
                    
                    if filename:
                        attachment_links.append({
                            'url': None,  # Will need to be handled by clicking
                            'filename': filename,
                            'element': element,
                            'onclick': onclick
                        })
                        print(f"Found onclick download: {filename}")
                    else:
                        # If no filename found in onclick, use element text as filename
                        element_text = element.text.strip()
                        if element_text and '.' in element_text:
                            attachment_links.append({
                                'url': None,
                                'filename': element_text,
                                'element': element,
                                'onclick': onclick
                            })
                            print(f"Found onclick download (from element text): {element_text}")
        
        except Exception as e:
            print(f"Error finding onclick downloads: {e}")
        
        # Method 4: Look for any download buttons or links (broader search)
        try:
            print("Looking for download buttons and links...")
            
            # Look for elements with download-related text or attributes
            download_elements = driver.find_elements(By.XPATH, 
                "//*[contains(text(), 'Download') or contains(text(), 'download') or "
                "contains(@class, 'download') or contains(@title, 'Download') or "
                "contains(@alt, 'Download') or text()='X']"
            )
            
            for element in download_elements:
                try:
                    # Try to find associated filename
                    filename = None
                    
                    # Check if element has a filename in its text
                    element_text = element.text.strip()
                    if element_text and '.' in element_text and len(element_text) < 100:
                        filename = element_text
                    
                    # If no filename in element text, look in nearby elements
                    if not filename:
                        # Look for filename in parent row or nearby elements
                        try:
                            parent_row = element.find_element(By.XPATH, "./ancestor::tr[1]")
                            row_text = parent_row.text
                            # Extract potential filenames from row text
                            import re
                            filename_matches = re.findall(r'[a-zA-Z0-9_-]+\.[a-zA-Z]{2,5}', row_text)
                            if filename_matches:
                                filename = filename_matches[0]
                        except:
                            pass
                    
                    if filename:
                        # Check if element is clickable
                        if (element.tag_name.lower() == 'a' or 
                            element.get_attribute('onclick') or 
                            element.get_attribute('href')):
                            
                            attachment_links.append({
                                'url': element.get_attribute('href'),
                                'filename': filename,
                                'element': element,
                                'onclick': element.get_attribute('onclick')
                            })
                            print(f"Found download element: {filename}")
                
                except Exception as e:
                    continue
        
        except Exception as e:
            print(f"Error finding download elements: {e}")
        
        # Remove duplicates based on filename (since URLs might be None)
        unique_links = []
        seen_filenames = set()
        for link in attachment_links:
            filename = link.get('filename', '')
            if filename and filename not in seen_filenames:
                unique_links.append(link)
                seen_filenames.add(filename)
        
        attachment_links = unique_links
        
        if not attachment_links:
            print("ERROR: No attachment links found on the page")
            
            # Debug: Show page source snippet
            print("\nPage title:", driver.title)
            print("Current URL:", driver.current_url)
            
            # Debug: Look for all tables on the page
            all_tables = driver.find_elements(By.XPATH, "//table")
            print(f"\nFound {len(all_tables)} table(s) on the page:")
            for i, table in enumerate(all_tables):
                try:
                    table_text = table.text[:200]  # First 200 chars
                    print(f"  Table {i+1}: {table_text}...")
                except:
                    print(f"  Table {i+1}: [Could not read text]")
            
            # Debug: Look for elements containing ESBD or specific filenames
            esbd_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'ESBD_') or contains(text(), '696-TC-25-P024')]")
            print(f"\nFound {len(esbd_elements)} elements containing ESBD or contract ID:")
            for elem in esbd_elements[:10]:  # Show first 10
                try:
                    print(f"  - {elem.tag_name}: '{elem.text}' (onclick: {elem.get_attribute('onclick')})")
                except:
                    pass
            
            # Debug: Look for all clickable elements (links and buttons)
            clickable_elements = driver.find_elements(By.XPATH, "//a[@href] | //button | //*[@onclick]")
            print(f"\nFound {len(clickable_elements)} clickable elements:")
            for elem in clickable_elements[:15]:  # Show first 15
                try:
                    text = elem.text.strip()[:50]  # First 50 chars
                    href = elem.get_attribute('href')
                    onclick = elem.get_attribute('onclick')
                    if text or href or onclick:
                        print(f"  - {elem.tag_name}: '{text}' href='{href}' onclick='{onclick}'")
                except:
                    pass
            
            return []
        
        print(f"\nFound {len(attachment_links)} attachment(s) to download")
        
        # Try to download files by clicking links or executing JavaScript
        downloaded_files = []
        
        for i, link_info in enumerate(attachment_links):
            try:
                print(f"\nDownloading file {i+1}/{len(attachment_links)}: {link_info['filename']}")
                
                # Scroll to the element
                driver.execute_script("arguments[0].scrollIntoView(true);", link_info['element'])
                time.sleep(1)
                
                # Check if this is an onclick handler or direct link
                if 'onclick' in link_info:
                    print(f"Executing onclick: {link_info['onclick']}")
                    # Execute the onclick JavaScript directly
                    try:
                        driver.execute_script(link_info['onclick'])
                    except Exception as e:
                        print(f"Failed to execute onclick, trying click: {e}")
                        link_info['element'].click()
                else:
                    # Regular click for direct links
                    link_info['element'].click()
                
                time.sleep(3)  # Wait for download to start
                
            except Exception as e:
                print(f"Error clicking download link {i+1}: {e}")
        
        # Wait for downloads to complete
        if attachment_links:
            downloaded_files = wait_for_downloads(download_dir, len(attachment_links), initial_files, timeout=120)
        
        # If selenium download failed, try direct requests download
        if len(downloaded_files) < len(attachment_links):
            print(f"\nSelenium downloaded {len(downloaded_files)}/{len(attachment_links)} files")
            print("Attempting direct download for missing files...")
            
            direct_downloads = download_files_with_requests(attachment_links, download_dir, contract_id)
            
            # Combine results, but filter to only include new files (not in initial_files)
            all_new_files = set(downloaded_files + direct_downloads) - set(initial_files)
            downloaded_files = list(all_new_files)
        
        print(f"\n=== DOWNLOAD COMPLETE ===")
        print(f"Contract ID: {contract_id}")
        print(f"Download directory: {download_dir}")
        print(f"New files downloaded this session: {len(downloaded_files)}")
        
        for filename in downloaded_files:
            file_path = os.path.join(download_dir, filename)
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                print(f"  - {filename} ({file_size} bytes)")
        
        # Extract text from downloaded files if requested
        if extract_text and downloaded_files:
            print(f"\n=== EXTRACTING TEXT ===")
            extracted_text = process_downloaded_files(downloaded_files, download_dir, contract_id)
            
            # Save extracted text to file
            text_output_dir = os.path.join(DOWNLOADS_DIR, contract_id)
            text_file = os.path.join(text_output_dir, f"{contract_id}_extracted_text.txt")
            
            try:
                with open(text_file, 'w', encoding='utf-8') as f:
                    f.write(extracted_text)
                print(f"Extracted text saved to: {text_file}")
            except Exception as e:
                print(f"Warning: Could not save extracted text: {e}")
            
            return {
                'files': downloaded_files,
                'text': extracted_text,
                'text_file': text_file if 'text_file' in locals() else None
            }
        else:
            return {
                'files': downloaded_files,
                'text': None,
                'text_file': None
            }
        
    except Exception as e:
        print(f"ERROR: Failed to download files: {e}")
        return {
            'files': [],
            'text': None,
            'text_file': None
        }
    
    finally:
        if driver:
            print("Closing browser...")
            driver.quit()

# Simple test function for when script is run directly

def main():
    """Simple test function."""
    # Test with the original URL
    test_url = "https://www.txsmartbuy.gov/esbd/717-26-705"
    print(f"Testing download_esbd_files function with: {test_url}")
    
    result = download_esbd_files(test_url, extract_text=True)
    
    if result['files']:
        print(f"\n✅ SUCCESS: Downloaded {len(result['files'])} files")
        for filename in result['files']:
            print(f"  - {filename}")
        
        if result['text']:
            print(f"\n📄 TEXT EXTRACTION: {len(result['text'])} characters extracted")
            print(f"Text saved to: {result['text_file']}")
            
            # Show first 500 characters as preview
            preview = result['text'][:500]
            print(f"\nText Preview:\n{'-'*40}")
            print(preview)
            if len(result['text']) > 500:
                print("...[truncated]")
        else:
            print("\n📄 No text extraction performed")
    else:
        print(f"\n❌ ERROR: No files were downloaded")

if __name__ == "__main__":
    main()
