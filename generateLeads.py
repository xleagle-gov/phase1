import json
import requests
import os
import hashlib
import re
from datetime import datetime, timedelta
from download_sam_files import download_files_from_sam_url, DOWNLOADS_DIR
from backfillfolderLinks import get_drive_access_token, create_drive_folder, upload_file_to_drive
import pygsheets
import time

# Import from centralized config and services
from config import DRIVE_PARENT_FOLDER_ID, TEXT_CACHE_DIR, CACHE_EXPIRY_HOURS, ENABLE_DRIVE_UPLOAD
from services.openai_service import generate_vendor_leads

# Google Drive folder for SAM.gov files (use centralized config)
SAM_GOV_PARENT_FOLDER_ID = DRIVE_PARENT_FOLDER_ID

# Cache configuration (use centralized config)
CACHE_DIR = TEXT_CACHE_DIR

def get_cache_filename(url):
    """Generate a cache filename based on the URL."""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return f"sam_text_{url_hash}.json"

def load_cached_text(url):
    """Load cached text if it exists and is not expired."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, get_cache_filename(url))
    
    if not os.path.exists(cache_file):
        return None
    
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        
        # Check if cache is expired
        cached_time = datetime.fromisoformat(cache_data['timestamp'])
        if datetime.now() - cached_time > timedelta(hours=CACHE_EXPIRY_HOURS):
            print("Cache expired, will re-download")
            return None
        
        print(f"SUCCESS: Loaded cached text ({len(cache_data['text'])} characters)")
        return cache_data['text']
        
    except Exception as e:
        print(f"Error loading cache: {e}")
        return None

def save_cached_text(url, text):
    """Save extracted text to cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, get_cache_filename(url))
    
    cache_data = {
        'url': url,
        'text': text,
        'timestamp': datetime.now().isoformat(),
        'text_length': len(text)
    }
    
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"SUCCESS: Saved text to cache ({len(text)} characters)")
    except Exception as e:
        print(f"Error saving cache: {e}")

def get_skip_cache_filename(url):
    """Generate a skip cache filename based on the URL."""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return f"sam_skip_{url_hash}.json"

def load_cached_skip(url):
    """Return the cached skip reason for a URL, or None if not skipped/expired."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, get_skip_cache_filename(url))

    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        cached_time = datetime.fromisoformat(cache_data['timestamp'])
        if datetime.now() - cached_time > timedelta(hours=CACHE_EXPIRY_HOURS):
            print("Skip cache expired, will re-check")
            return None

        reason = cache_data.get('skip_reason')
        print(f"Loaded cached skip reason: {reason}")
        return reason

    except Exception as e:
        print(f"Error loading skip cache: {e}")
        return None

def save_cached_skip(url, skip_reason):
    """Persist the skip reason for a URL so it isn't re-processed on reruns."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, get_skip_cache_filename(url))

    cache_data = {
        'url': url,
        'skip_reason': skip_reason,
        'timestamp': datetime.now().isoformat(),
    }

    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"Saved skip reason to cache: {skip_reason}")
    except Exception as e:
        print(f"Error saving skip cache: {e}")

def upload_sam_files_to_drive(url):
    """
    Upload downloaded SAM.gov files to Google Drive and return the folder link.
    
    Parameters:
    - url: SAM.gov URL to extract notice ID from
    
    Returns:
    - folder_link: Google Drive folder link, or None if failed
    """
    try:
        # Extract notice ID from URL
        match = re.search(r'/opp/([^/]+)/view', url)
        if not match:
            print("Could not extract notice ID from URL for Drive upload")
            return None
        
        notice_id = match.group(1)
        print(f"\n📁 Uploading files to Google Drive for notice: {notice_id}")
        
        # Check if files exist in the extracted folder
        extract_dir = os.path.join(DOWNLOADS_DIR, "extracted", notice_id)
        download_dir = os.path.join(DOWNLOADS_DIR, notice_id)
        
        # Collect all files to upload
        files_to_upload = []
        
        # Check extracted directory first
        if os.path.exists(extract_dir):
            for filename in os.listdir(extract_dir):
                file_path = os.path.join(extract_dir, filename)
                if os.path.isfile(file_path):
                    files_to_upload.append(file_path)
        
        # Also check for zip files in the download directory
        if os.path.exists(download_dir):
            for filename in os.listdir(download_dir):
                file_path = os.path.join(download_dir, filename)
                if os.path.isfile(file_path):
                    files_to_upload.append(file_path)
        
        if not files_to_upload:
            print(f"⚠️ No files found to upload for notice {notice_id}")
            return None
        
        print(f"Found {len(files_to_upload)} files to upload")
        
        # Get Google Drive access token
        access_token = get_drive_access_token()
        if not access_token:
            print("❌ Failed to get Google Drive access token")
            return None
        
        # Create folder in Google Drive
        folder_name = f"SAM_{notice_id}"
        folder_id, folder_link = create_drive_folder(access_token, folder_name, SAM_GOV_PARENT_FOLDER_ID)
        
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
        print(f"❌ Error uploading files to Google Drive: {e}")
        return None

def process_single_solicitation(url, resource_links=None, notice_id=None):
    """
    Process a single SAM.gov URL and return structured data.
    
    Parameters:
    - url: The SAM.gov opportunity URL
    - resource_links: Optional list of direct download URLs from the API (for faster downloads)
    - notice_id: Optional notice ID (extracted from URL if not provided)
    
    Returns:
    - Dict with sam_gov_link, emails, subject, body
    """
    
    print(f"Processing SAM.gov URL: {url}")
    if resource_links:
        print(f"  📥 {len(resource_links)} resource links available for API download")

    # Step 0: Check if this URL was previously skipped
    print("Step 0: Checking for cached skip reason...")
    cached_skip = load_cached_skip(url)
    if cached_skip:
        print(f"Skipping contract (cached): {cached_skip}")
        return {
            "sam_gov_link": url,
            "emails": "skipped",
            "subject": "skipped",
            "body": cached_skip
        }

    # Step 1: Try to load cached text first
    print("Step 1: Checking for cached text...")
    complete_text = load_cached_text(url)
    
    if not complete_text:
        # Step 1a: Get all extracted text from SAM.gov (UI text + all attachment files)
        print("Step 1a: No cache found, extracting all text from SAM.gov...")
        # Pass resource_links for API-based download (faster than Selenium)
        raw = download_files_from_sam_url(url, resource_links=resource_links, notice_id=notice_id)
        complete_text, download_extra = (raw[0], raw[1]) if isinstance(raw, tuple) else (raw, {})
        folder_link_for_sheet = download_extra.get("piee_link")  # PIEE link when files could not be extracted
        if complete_text in (
            "contract is due within 3 days or already past due or is dibbs",
            "contract requires mandatory site visit",
            "contract has controlled attachments"
        ):
            print(f"Skipping contract: {complete_text}")
            save_cached_skip(url, complete_text)
            return {
                "sam_gov_link": url,
                "emails": "skipped",
                "subject": "skipped",
                "body": complete_text
            }
        if not complete_text:
            print("ERROR: Could not extract text from SAM.gov URL")
            return {
                "sam_gov_link": url,
                "emails": "ERROR: Could not extract text",
                "subject": "ERROR: Could not extract text",
                "body": "ERROR: Could not extract text"
            }
        print(f"SUCCESS: Extracted {len(complete_text)} characters of text from SAM.gov")
        save_cached_text(url, complete_text)
    else:
        print("Using cached text (skipping download and extraction)")
        folder_link_for_sheet = None
    
    # Step 2: Generate vendor leads using centralized OpenAI service
    print("Step 2: Generating vendor leads using OpenAI service...")
    
    # Use the centralized service with " k2" suffix (same as before)
    lead_result = generate_vendor_leads(
        solicitation_text=complete_text,
        source="SAM.GOV",
        subject_suffix=" k2"
    )
    
    # Return structured data with sam_gov_link
    result = {
        "sam_gov_link": url,
        "emails": lead_result["emails"],
        "subject": lead_result["subject"],
        "body": lead_result["body"]
    }
    if folder_link_for_sheet:
        result["folder_link"] = folder_link_for_sheet  # PIEE link when files could not be extracted

    print(f"Successfully processed: {url}")
    return result

def getLeadsForMultipleSolicitations(records,sh):

    """Process multiple SAM.gov URLs and return structured results."""
    
    print(f"Processing {len(records)} solicitation(s)...")
    results = []
    rowNumber=1
    for record in records:
        rowNumber+=1
        if(record['getEmails'].lower()!='yes'):
            continue
        print(f"Processing row {rowNumber}")
        url=record['Sam Link']
        result = process_single_solicitation(url)
        if(result["emails"]=="skipped"):
            sh.update_value('K'+str(rowNumber), "skipped")
            continue
        # result={'emails': 'sales@containernv.com; selectcontainers@gmail.com; hello@seatrains.com; kustomoffice@gmail.com; zionscustomcontainers@gmail.com; sales@containsupply.com; Info@SickFab.com; tradcon@cox.net; info@crownfabricators.com; info@silverstatestainless.com; Tim@775Fabrication.com; sales@conexwest.com; info@OnSiteStorage.com; websales@container.com; reno@azteccontainer.com; sales@globalcontainergroup.us; GBOHomes@gmail.com; info@curpool.com; metalmfg@sbcglobal.net; thecontainerworx@gmail.com; sales@super-box.us; info@xleagle.com', 'subject': 'Request for Quote: ACFT Equipment Storage Containers (W9124X25Q0019-0917)', 'body': 'Hi,\n\nWe’re requesting a quote to build and deliver five portable, weather‑tight storage containers for Army fitness equipment. Units must be steel or aluminum, corrosion‑resistant, with ventilation in accordance with MIL‑STD‑648, forklift pockets/corner fittings, lockable double doors, interior custom racks/shelving for the listed equipment, exterior 17th STB logo, and IUID exterior marking.\n\nLocation (tilt‑bed delivery/offload required):\n– Speedway Readiness Center, 7005 N Hollywood Blvd, Las Vegas, NV 89115 (1 each, capacity: 16 ACFT sets)\n– Las Vegas Readiness Center, 4500 W Silverado Ranch Blvd, Las Vegas, NV 89139 (1 each, 16 sets)\n– North Las Vegas Readiness Center, 6600 Range Rd, Las Vegas, NV 89165 (1 each, 16 sets)\n– Henderson Armory, 151 E Horizon Ridge Pkwy, Henderson, NV 89002 (1 each, 5 sets)\n– Washoe County Armory, 19980 Army Aviation Dr, Reno, NV 89506 (1 each, 20 sets)\n\nEach ACFT set consists of: 1 hexagon barbell; 4×10‑lb plates; 2×15‑lb; 2×25‑lb; 8×45‑lb; 2×40‑lb kettlebells; 1×10‑lb medicine ball; 1 measuring tape reel; 1 nylon sled; 1 pair barbell collars.\n\nScope Includes:\n– Fabrication or modification of portable container(s) with interior racking/shelving laid out for the counts above\n– Weather‑tight seals, desert‑grade ventilation (MIL‑STD‑648), sun/UV‑resistant exterior\n– Exterior paint and unit branding (17th STB logo); IUID marking on each unit\n– Tilt‑bed delivery/offload to each address\n\nPeriod of Performance: Estimated Oct–Nov 2025 (coordinate exact delivery dates with us)\n\nPlease confirm availability, estimated lead time, drawings/tech specs, warranty, and a delivered price per site. All quotes due by: September 16, 2025, 2:00 PM Pacific.\n\nThanks,\nAvinash Nayak\nChief Operating Officer\nXL Eagle\ninfo@xleagle.com\n(608) 999‑1679\n2021 Guadalupe St, Suite 260, Austin, TX 78705'}
        results.append(result)
        # sh.update_acell(f'k{rowNumber}', "generated")
        # sh.update_acell(f'i{rowNumber}', result['emails'])
        # sh.update_acell(f'g{rowNumber}', result['subject'])
        # sh.update_acell(f'h{rowNumber}', result['body'])
        if(record["Google Drive Folder Link"]=="DVOSB"):
            result["subject"]=result["subject"]+" - DVOSB"
        sh.update_value('K'+str(rowNumber), "generated")
        sh.update_value('I'+str(rowNumber), result['emails'])
        sh.update_value('G'+str(rowNumber), result['subject'])
        sh.update_value('H'+str(rowNumber), result['body'])
        
        # Upload downloaded files to Google Drive and update column L
        if ENABLE_DRIVE_UPLOAD:
            print(f"📤 Uploading SAM.gov files to Google Drive...")
            drive_folder_link = upload_sam_files_to_drive(url)
            if drive_folder_link:
                sh.update_value('L'+str(rowNumber), drive_folder_link)
                print(f"✅ Updated column L with Drive folder link")
            else:
                sh.update_value('L'+str(rowNumber), "No files uploaded")
                print(f"⚠️ No files were uploaded to Google Drive")
        
        time.sleep(5)
        
    # for i, url in enumerate(urls, 1):
    #     print(f"\n{'='*100}")
    #     print(f"PROCESSING SOLICITATION {i}/{len(urls)}")
    #     print(f"{'='*100}")
        
    #     result = process_single_solicitation(url)
    #     results.append(result)
        
    #     print(f"Completed {i}/{len(urls)} solicitations")
    
    print(f"\n{'='*100}")
    print("ALL SOLICITATIONS PROCESSED")
    print(f"{'='*100}")
    print(f"Total results: {len(results)}")
    
    return results


# Backward compatibility - keep the original function name
def getLeadsForSamUrl(url):
    """Get all text from SAM.gov URL and generate vendor leads using GPT with web search."""
    return process_single_solicitation(url)

# Main execution
if __name__ == "__main__":
    # Example usage with multiple solicitations
    solicitation_urls = [
        "https://sam.gov/workspace/contract/opp/b4172d13dd654bbba091befc9c2366d4/view",
        # Add more URLs as needed
    ]
    gc = pygsheets.authorize(service_file='key.json')
    sh = gc.open('Quote Request')
    wks = sh.worksheet_by_title('SAM.GOV')
    records = wks.get_all_records()
    # records=records.filter(lambda x: x['getEmails']=='Yes')
    # Process multiple solicitations
    results = getLeadsForMultipleSolicitations(records,wks)
    
    # Print results in structured format
    print(f"\n{'='*120}")
    print("FINAL RESULTS - STRUCTURED OUTPUT")
    print(f"{'='*120}")
    
    for i, result in enumerate(results, 1):
        print(f"\n--- SOLICITATION {i} ---")
        print(f"SAM.gov Link: {result['sam_gov_link']}")
        print(f"Emails: {result['emails']}")
        print(f"Subject: {result['subject']}")
        print(f"Body: {result['body'][:200]}..." if len(str(result['body'])) > 200 else f"Body: {result['body']}")
        print("-" * 80)
    
    # Example: Save results to CSV for easy viewing
    import csv
    csv_filename = "solicitation_results.csv"
    with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['sam_gov_link', 'emails', 'subject', 'body']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result)
    
    print(f"\n✅ Results also saved to {csv_filename}")
    
    # For single URL processing (backward compatibility)
    # single_result = getLeadsForSamUrl("https://sam.gov/workspace/contract/opp/b4172d13dd654bbba091befc9c2366d4/view")
    # print("Single result:", single_result)
